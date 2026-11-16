package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"wiwi-go/internal/config"
)

func TestSingleflightCollapses(t *testing.T) {
	var calls int32
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&calls, 1)
		time.Sleep(30 * time.Millisecond)
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"choices": []map[string]any{{"message": map[string]any{"content": "ok"}, "finish_reason": "stop"}},
			"usage":   map[string]any{"prompt_tokens": 1, "completion_tokens": 1},
		})
	}))
	defer up.Close()

	cfg := &config.Config{}
	cfg.General.MasterKey = "sk-test"
	cfg.General.DatabaseURL = "sqlite:///" + filepath.Join(t.TempDir(), "sf.db")
	cfg.Wiwi.SingleflightEnabled = true
	cfg.Providers = []config.Provider{{
		Name: "p", ProviderType: "openai", BaseURL: up.URL,
		Keys: []config.KeyDef{{Label: "k", Key: "x", Weight: 1, Enabled: true}},
	}}
	cfg.ModelList = []config.ModelEntry{{
		ModelName: "m", Params: config.DeploymentParams{Provider: "p", Model: "m", Weight: 1},
	}}
	app, err := New(cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer app.Close()

	var wg sync.WaitGroup
	for i := 0; i < 5; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			w := httptest.NewRecorder()
			req, _ := http.NewRequest("POST", "/v1/chat/completions",
				strings.NewReader(`{"model":"m","messages":[{"role":"user","content":"hi"}]}`))
			req.Header.Set("Content-Type", "application/json")
			req.Header.Set("Authorization", "Bearer sk-test")
			app.Router.ServeHTTP(w, req)
		}()
	}
	wg.Wait()
	if n := atomic.LoadInt32(&calls); n > 2 {
		t.Fatalf("expected singleflight to collapse, upstream calls=%d", n)
	}
}
