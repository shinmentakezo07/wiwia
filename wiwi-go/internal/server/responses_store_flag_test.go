package server

import (
	"encoding/json"
	"testing"

	"wiwi-go/internal/config"
)

func responseID(t *testing.T, app *App, body string) string {
	t.Helper()
	w := do(t, app, "POST", "/v1/responses", body)
	if w.Code != 200 {
		t.Fatalf("responses: %d %s", w.Code, w.Body.String())
	}
	var out map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &out); err != nil {
		t.Fatal(err)
	}
	id, _ := out["id"].(string)
	if id == "" {
		t.Fatalf("no response id: %s", w.Body.String())
	}
	return id
}

func TestResponsesStoreFalseIsNotPersisted(t *testing.T) {
	app, _ := newCountingApp(t, nil)
	id := responseID(t, app, `{"model":"gpt-test","input":"hi","store":false}`)
	if w := do(t, app, "GET", "/v1/responses/"+id, ""); w.Code != 404 {
		t.Fatalf("store=false response was persisted: %d %s", w.Code, w.Body.String())
	}
}

func TestResponsesCacheHitIsPersisted(t *testing.T) {
	app, _ := newCountingApp(t, func(c *config.Config) {
		c.Cache.Enabled = true
		c.Cache.TTLS = 60
	})
	first := responseID(t, app, `{"model":"gpt-test","input":"same","store":true}`)
	second := responseID(t, app, `{"model":"gpt-test","input":"same","store":true}`)
	if first == second {
		t.Fatal("expected the cache hit to rebase onto a new id")
	}
	if w := do(t, app, "GET", "/v1/responses/"+second, ""); w.Code != 200 {
		t.Fatalf("cache-hit response not resolvable: %d %s", w.Code, w.Body.String())
	}
}
