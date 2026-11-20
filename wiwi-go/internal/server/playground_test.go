package server

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func doWithCookie(t *testing.T, app *App, method, path, body, cookie string) *httptest.ResponseRecorder {
	t.Helper()
	w := httptest.NewRecorder()
	req, _ := http.NewRequest(method, path, strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	if cookie != "" {
		req.AddCookie(&http.Cookie{Name: "wiwi_session", Value: cookie})
	}
	app.Router.ServeHTTP(w, req)
	return w
}

func livePlaygroundKeys(t *testing.T, app *App) int {
	t.Helper()
	var n int
	if err := app.State.Store.QueryRow(app.State.Store.Rebind(
		`SELECT COUNT(*) FROM vkeys WHERE key_alias='playground'
		 AND COALESCE(disabled,0)=0 AND (expires_at IS NULL OR expires_at > ?)`),
		float64(time.Now().UnixNano())/1e9).Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}

const playgroundBody = `{"model":"gpt-test","messages":[{"role":"user","content":"hi"}]}`

// The Playground SPA posts with only its session cookie. The Go backend used
// to register the route behind requireAPI, so a logged-in browser got
// "invalid or missing API key".
func TestPlaygroundAcceptsSessionCookie(t *testing.T) {
	app, calls := newCountingApp(t, nil)
	u, err := app.State.Auth.CreateUser("alice", "pw", "user")
	if err != nil {
		t.Fatal(err)
	}
	sess, err := app.State.Auth.NewSession(app.State.SessionSecret, u.ID, 86400)
	if err != nil {
		t.Fatal(err)
	}

	w := doWithCookie(t, app, "POST", "/v1/playground/completions", playgroundBody, sess)
	if w.Code != 200 {
		t.Fatalf("playground: %d %s", w.Code, w.Body.String())
	}
	if *calls != 1 {
		t.Fatalf("upstream calls = %d, want 1", *calls)
	}

	var owner string
	if err := app.State.Store.QueryRow(app.State.Store.Rebind(
		`SELECT owner_id FROM vkeys WHERE key_alias='playground'`)).Scan(&owner); err != nil {
		t.Fatal(err)
	}
	if owner != u.ID {
		t.Fatalf("playground key owner = %q, want %q", owner, u.ID)
	}

	// A second turn must reuse the cached key, not mint a rival one.
	w = doWithCookie(t, app, "POST", "/v1/playground/completions", playgroundBody, sess)
	if w.Code != 200 {
		t.Fatalf("second playground: %d %s", w.Code, w.Body.String())
	}
	if n := livePlaygroundKeys(t, app); n != 1 {
		t.Fatalf("live playground keys = %d, want 1", n)
	}
}

func TestPlaygroundWithoutSessionIs401(t *testing.T) {
	app, calls := newCountingApp(t, nil)
	w := doWithCookie(t, app, "POST", "/v1/playground/completions", playgroundBody, "")
	if w.Code != 401 {
		t.Fatalf("anonymous playground: %d %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "authentication required") {
		t.Fatalf("unexpected body: %s", w.Body.String())
	}
	if *calls != 0 {
		t.Fatalf("upstream calls = %d, want 0", *calls)
	}
}

func TestPlaygroundBearerStillWorks(t *testing.T) {
	app, _ := newCountingApp(t, nil)
	w := do(t, app, "POST", "/v1/playground/completions", playgroundBody)
	if w.Code != 200 {
		t.Fatalf("bearer playground: %d %s", w.Code, w.Body.String())
	}
}

// /auth/playground-key hands the caller a key; the wrapper must reuse that
// exact key instead of minting a second one.
func TestPlaygroundKeyEndpointIsReusedByWrapper(t *testing.T) {
	app, _ := newCountingApp(t, nil)
	u, err := app.State.Auth.CreateUser("bob", "pw", "user")
	if err != nil {
		t.Fatal(err)
	}
	sess, err := app.State.Auth.NewSession(app.State.SessionSecret, u.ID, 86400)
	if err != nil {
		t.Fatal(err)
	}
	w := doWithCookie(t, app, "POST", "/auth/playground-key", "", sess)
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"key"`) {
		t.Fatalf("mint: %d %s", w.Code, w.Body.String())
	}
	if n := livePlaygroundKeys(t, app); n != 1 {
		t.Fatalf("live playground keys after mint = %d, want 1", n)
	}
	w = doWithCookie(t, app, "POST", "/v1/playground/completions", playgroundBody, sess)
	if w.Code != 200 {
		t.Fatalf("wrapper after mint: %d %s", w.Code, w.Body.String())
	}
	if n := livePlaygroundKeys(t, app); n != 1 {
		t.Fatalf("wrapper minted a rival key: live = %d, want 1", n)
	}
}

// The per-owner cap retires the oldest playground key, mirroring Python.
func TestPlaygroundKeyCap(t *testing.T) {
	app, _ := newCountingApp(t, nil)
	u, err := app.State.Auth.CreateUser("carol", "pw", "user")
	if err != nil {
		t.Fatal(err)
	}
	sess, err := app.State.Auth.NewSession(app.State.SessionSecret, u.ID, 86400)
	if err != nil {
		t.Fatal(err)
	}
	for i := range maxPlaygroundKeysPerOwn + 1 {
		w := doWithCookie(t, app, "POST", "/auth/playground-key", "", sess)
		if w.Code != 200 {
			t.Fatalf("mint %d: %d %s", i, w.Code, w.Body.String())
		}
	}
	if n := livePlaygroundKeys(t, app); n != maxPlaygroundKeysPerOwn {
		t.Fatalf("live playground keys = %d, want %d", n, maxPlaygroundKeysPerOwn)
	}
}

// The synthetic master has no users row; its key must therefore stay unowned
// or Authenticate fails closed and the playground 401s.
func TestPlaygroundMasterSessionMintsUnownedKey(t *testing.T) {
	app, _ := newCountingApp(t, nil)
	sess, err := app.State.Auth.NewMasterSession(app.State.SessionSecret, 86400)
	if err != nil {
		t.Fatal(err)
	}
	w := doWithCookie(t, app, "POST", "/v1/playground/completions", playgroundBody, sess)
	if w.Code != 200 {
		t.Fatalf("master playground: %d %s", w.Code, w.Body.String())
	}
	var owner *string
	if err := app.State.Store.QueryRow(app.State.Store.Rebind(
		`SELECT owner_id FROM vkeys WHERE key_alias='playground'`)).Scan(&owner); err != nil {
		t.Fatal(err)
	}
	if owner != nil && *owner != "" {
		t.Fatalf("master playground key owner = %q, want NULL", *owner)
	}
}
