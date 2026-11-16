package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// doAuth issues a request with explicit headers/cookies, bypassing the shared
// do() helper (which always attaches a master bearer).
func doAuth(t *testing.T, app *App, method, path, body string, headers map[string]string, cookies []*http.Cookie) *httptest.ResponseRecorder {
	t.Helper()
	w := httptest.NewRecorder()
	req, _ := http.NewRequest(method, path, strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	for _, c := range cookies {
		req.AddCookie(c)
	}
	app.Router.ServeHTTP(w, req)
	return w
}

func sessionCookie(t *testing.T, w *httptest.ResponseRecorder) *http.Cookie {
	t.Helper()
	for _, c := range w.Result().Cookies() {
		if c.Name == "wiwi_session" {
			return c
		}
	}
	t.Fatalf("login did not set wiwi_session (cookies: %v)", w.Result().Cookies())
	return nil
}

// TestMasterKeyLogin covers the regression where the Go backend's /auth/login
// ignored the master_key field, so the console's "Master key" tab always got a
// 401 ("master key required or invalid").
func TestMasterKeyLogin(t *testing.T) {
	app, _ := newTestApp(t) // master key is "sk-test"

	w := doAuth(t, app, "POST", "/auth/login", `{"master_key":"sk-test"}`, nil, nil)
	if w.Code != http.StatusOK {
		t.Fatalf("master login: %d %s", w.Code, w.Body.String())
	}
	var resp struct {
		User struct {
			ID       string `json:"id"`
			Username string `json:"username"`
			Role     string `json:"role"`
		} `json:"user"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatal(err)
	}
	if resp.User.ID != "master" || resp.User.Role != "admin" {
		t.Fatalf("master user: %+v", resp.User)
	}
	cookie := sessionCookie(t, w)

	// The console keeps the master key as a bearer; /auth/me must honour it.
	w = doAuth(t, app, "GET", "/auth/me", "", map[string]string{"Authorization": "Bearer sk-test"}, nil)
	if w.Code != http.StatusOK || !strings.Contains(w.Body.String(), `"id":"master"`) {
		t.Fatalf("/auth/me bearer: %d %s", w.Code, w.Body.String())
	}
	// ...and the session cookie from login must resolve the same actor.
	w = doAuth(t, app, "GET", "/auth/me", "", nil, []*http.Cookie{cookie})
	if w.Code != http.StatusOK || !strings.Contains(w.Body.String(), `"id":"master"`) {
		t.Fatalf("/auth/me cookie: %d %s", w.Code, w.Body.String())
	}
	// The master session cookie is admin, so /admin/* works via cookie too.
	w = doAuth(t, app, "GET", "/admin/keys", "", nil, []*http.Cookie{cookie})
	if w.Code != http.StatusOK {
		t.Fatalf("/admin/keys via master cookie: %d %s", w.Code, w.Body.String())
	}
}

func TestMasterKeyLoginWrongKey(t *testing.T) {
	app, _ := newTestApp(t)
	w := doAuth(t, app, "POST", "/auth/login", `{"master_key":"not-the-key"}`, nil, nil)
	if w.Code != http.StatusUnauthorized {
		t.Fatalf("wrong master key: %d %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "invalid master key") {
		t.Fatalf("wrong master key body: %s", w.Body.String())
	}
}

func TestUsernameLoginStillWorks(t *testing.T) {
	app, _ := newTestApp(t)
	w := doAuth(t, app, "POST", "/auth/signup", `{"username":"alice","password":"password123"}`, nil, nil)
	if w.Code != http.StatusOK {
		t.Fatalf("signup: %d %s", w.Code, w.Body.String())
	}
	w = doAuth(t, app, "POST", "/auth/login", `{"username":"alice","password":"password123"}`, nil, nil)
	if w.Code != http.StatusOK || !strings.Contains(w.Body.String(), `"username":"alice"`) {
		t.Fatalf("login: %d %s", w.Code, w.Body.String())
	}
}
